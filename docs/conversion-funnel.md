# Conversion Funnel — Implementation Notes

*Last updated: 2026-09-16. Covers all uncommitted changes in this branch: the
funnel plan at `.kilo/plans/conversion-funnel.md`, the follow-up fixes, the
contact tracking, the API usage accounting, the customer-stage work
(`inquiry`/`customer` stages, `services_offer` nudge, `mark-customer` CLI),
and the intent-hygiene pass (contact navigation vs. mailto intent, once-per-
user gate events, `api_consumer` at first use).*

---

## Why this work exists

MeshWeave's product story is a funnel: an anonymous visitor runs a free
analysis, a registered user saves and re-runs analyses for their clients, and
an agency eventually plugs our bulk API into their pipeline. Two business
plans live on top of that funnel:

- **Plan A — Services**: agencies hire us (or MeshWeave directly) to do the
  remediation work. The website is the pitch; the report and diff exports are
  the artifacts an agency hands to a client.
- **Plan B — Paid API**: if services don't close, the API becomes the product.

The choice between the two is explicitly open. That is the whole point of
this work: **we cannot pick a plan without trustworthy funnel data**, and we
cannot pitch agencies without client-ready artifacts. Everything below serves
one of those two needs.

Three ground rules shaped every decision. These are settled team calls —
the final decision was the owner's, after discussion:

1. **Known users only.** The funnel tracks identified accounts. Anonymous
   activity — submission volume, page views, CTA impressions — lives in
   aggregate Prometheus counters and is never tied to an identity. There is
   no cookie stitching, no IP-based actor tracking, no identity merging. If
   someone deletes their account, their funnel history goes with it.
2. **Bearer-only API.** The plan originally allowed session-or-Bearer auth
   on the v1 API. The owner dropped the session half: browser-session
   access to the API is a separate, deliberately deferred problem. Internal
   callers (the result-page JS) keep using the older session-authenticated
   routes until that is solved.
3. **No free-API rate limits yet.** The bulk API ships unmetered on purpose:
   collect usage data first, decide limits later. (See the usage tables
   added near the end of this document — they are the "later" part.)

---

## The data model, in plain language

Four new tables (`funnel_events`, `funnel_state`,
`funnel_actor_domains`, `api_key_usage_daily`), one new column
(`api_keys.first_used_at`), plus the queue columns on `crawls`.

### `funnel_events` — the raw history

One row per event, for a known user. Event types: `user_registered`,
`analysis_saved`, `analysis_claimed`, `analysis_completed`,
`analysis_failed`, `recheck_completed`, `report_exported`,
`diff_exported`, `contact_mailto_clicked`, `gated_feature_hit`,
`gated_feature_taken`, `api_key_created`, `api_key_first_used`,
`nudge_dismissed`, `segment_upgraded`, `became_customer`.

Each row carries the user id, an optional crawl id, a denormalized domain
(so agency counting never needs a join), and a small JSON payload for
context (scope, feature, nudge name, export format). Indexes cover the two
questions we ask most: "what did this user do, in order" and "who did this
kind of thing, recently."

The design rule that matters: **events are written inside the caller's
database transaction.** A `analysis_completed` event commits with the crawl
row's terminal state update or not at all. An event can never exist for a
state change that rolled back, and a state change can never commit with its
event lost. This is why every `emit` call takes the caller's session — and
why a `try/except` around metrics *counters* is fine while the event insert
itself is never swallowed.

### `funnel_state` — one row per user

The roll-up: current stage (`registered` → `api_consumer` → `inquiry` →
`customer`, monotonic — nobody ever moves backwards, and a customer is
terminal), segment (`standard` or `agency`), counters
(`analyses_count`, `distinct_domains`), the dismissal timestamps that
drive nudge resurfacing, and the gate-offer bookkeeping
(`gates_seen`, `gates_taken`) that keeps those events once per user
per feature. Nudge selection reads this row; it is cheap.

### `funnel_actor_domains` — agency detection

A (user, domain) pair set, one row per distinct domain the user has
completed an analysis on. Five distinct domains flips the user's segment to
`agency` and emits `segment_upgraded` exactly once. The insert is
`ON CONFLICT DO NOTHING`, so re-running the same domain never re-counts —
an agency re-auditing one client's site ten times is one domain.

### `api_keys.first_used_at`

A nullable timestamp, stamped the moment a key is first presented
successfully — that event, `api_key_first_used`, is what moves the stage
to `api_consumer` and is the activation signal. Creation alone
(`api_key_created`) does not promote: a user who makes a key and never
calls is still `registered` when reading the stage. The stamp uses a
conditional UPDATE
(`WHERE first_used_at IS NULL`) so two concurrent first calls cannot both
"win"; only the request that flips the column emits the event. The
activation metric — the single most important conversion number in the
funnel — is therefore exactly-once by construction.

### `api_key_usage_daily` — usage accounting (added 2026-09-14)

One row per key per UTC day: `calls`, `urls_admitted`, `urls_rejected`.
Written by a server-side atomic upsert (`INSERT ... ON CONFLICT DO UPDATE`
with `col = col + excluded.col`), best-effort in a try/except so a usage
accounting failure can never break an API call. This is the evidence base
for the deferred rate-limit decision: after a few weeks of real traffic,
p50/p95 calls-per-key-per-day and the admitted/rejected split tell us what
limits (if any) are fair, instead of guessing.

Why the split matters: `urls_rejected` tells a story on its own. High
rejections with `quota` reasons mean real users are hitting the concurrent
or daily caps (limits may be too tight). High `invalid` rejections mean
callers are misusing the batch shape or probing internal targets (the SSRF
guard doing its job).

---

## The API surface (v1)

Five endpoints under `/api/v1/`, all Bearer-key only, all owner-scoped (a
key sees only its owner's crawls — cross-owner access returns 404, never a
leaky 403):

- `POST /api/v1/analyses` — bulk submit up to 25 URLs, page or site scope.
  Each URL is admitted or rejected independently (`queued | cooldown |
  quota | invalid`); the batch never fails wholesale. This is the
  agency-pipeline endpoint.
- `GET /api/v1/analyses` — the caller's crawl list, keyset-paginated.
- `GET /api/v1/analyses/{id}` — the site-side contract (factors with
  weights, lens ratings, interpretation text, answerability evidence);
  202 with status while the crawl is in progress.
- `GET .../report.md` — the unbranded, client-ready report.
- `GET .../diff` / `.../diff.md` — the proof-of-work diff against the
  previous revision (or an explicit `?vs=`). Runs scored under different
  scoring versions are refused (409), never compared.

### What "unbranded" means here

The report export renders with the site name and contact email blanked.
No MeshWeave header, no footer contact path, no pitch. An agency can put
their own cover page on it and hand it to their client as their work. A
test (`test_report_has_no_meshweave_branding` in `tests/test_api_v1.py`)
asserts this by string-matching the rendered output for branding and
contact strings, so a future template change that re-adds branding fails
the suite rather than shipping.

### What the diff claims, honestly

Per-fix scores are not measurable between two runs — multiple fixes land
together, outside changes mix in, and only lens totals are observed. So
the diff exports state this explicitly: predicted deltas are **per fix**,
observed deltas are **per lens** (aggregate). The JSON payload carries a
`delta_granularity` field saying so, the markdown carries a footer note,
and the HTML page carries the same note above the findings and inline on
each prediction line. The export never claims precision it does not have.

### Anti-abuse, in layers

The bulk API is free and unmetered, which means the safeguards have to be
real:

- **SSRF guard** (`reject_internal_target` in `webapp/utils/url.py`): every
  submitted URL's host must resolve to a globally routable IP. Literal
  IPs are checked against `is_global` (kills loopback, private ranges,
  link-local — including the cloud metadata address `169.254.169.254`).
  Hostnames are resolved and *every* returned address is checked (a DNS
  record that resolves to an internal IP is rejected even if another
  record is public). DNS failure rejects closed. Local names like
  `localhost`, `.internal`, `.local` are rejected outright. This guard is
  wired into both the API path and the browser form paths — page and site
  scope both.
- **Batch cap**: 25 URLs per call, plus a 64 KB body-size cap before
  parsing (413), and non-dict JSON bodies get a clean 400 instead of a 500.
- **Per-user quotas**: concurrent-jobs and daily-site-crawls limits apply,
  and a batch counts as batch-atomic — the whole batch's admissions count
  against the limit as they happen, so 25 URLs cannot sneak past a
  limit-of-2 one row at a time.
- **Cooldown**: re-submitting a target that ran recently (default 60 min)
  returns `cooldown` rather than re-crawling.
- **Ownership**: every read/write path re-verifies ownership; a key only
  ever touches its owner's rows.

What is deliberately absent: per-IP rate limiting, per-key rate limiting,
and crawl-delay pacing on the target side. The first two wait for the usage
data described above. The target-side pacing is the crawler's existing
politeness settings (unchanged).

---

## The durable crawl queue

Form submissions ride `BackgroundTasks` — fast, but lost on restart. Bulk
API batches cannot afford that, so they get a real queue.

The design is deliberately boring: queue state lives *on the crawl row*
(`queue_status`: `pending` → `running` → `done`/`failed`), claimed by a
conditional UPDATE, processed by a long-lived asyncio worker in the FastAPI
lifespan. The claim UPDATE also resets a stranded `running` crawl back to
`pending` in the same atomic statement. The failure/recovery matrix:

- **Worker crashes mid-crawl**: the queue job goes stale and is reclaimed
  to `pending` on the next poll; the claim resets the crawl to `pending`;
  it re-runs. Stale reclaim runs every poll (not just at startup), so a
  long-lived process also recovers jobs orphaned by a crashed co-worker.
- **Worker dies after the crawl finished but before the queue job closed**:
  the re-claimed job sees the crawl already `succeeded`/`failed` and just
  closes out — it never re-crawls a terminal row. This is the
  exactly-once guarantee: `analysis_completed` cannot double-emit from a
  crash-restart.
- **A crawl hangs forever**: a 15-minute per-job timeout fails it
  (`queue job timed out`) instead of blocking the queue — jobs in a batch
  run concurrently, so one slow target does not stall the other 24.
- **Nothing left stranded**: every path that marks a crawl terminal also
  marks its queue job terminal; retired rows (a newer run replaced them)
  have their queue job dropped so a retired row is never crawled behind
  the user's back.

## The revision invariant, defended

Exactly one `is_latest = True` row may exist per (owner, domain, path,
query, scope) series. The cooldown lookup, revision history, and diff
feature all depend on this — and one of the review findings was that bulk
submit violated it (inserted a new latest row without retiring the old
one, corrupting history and eventually 500ing the lookup). The fix: bulk
admission now retires the previous latest row exactly like the form path
always did, and cancels the retired row's pending queue job. A regression
test submits the same URL three times and asserts exactly one latest row
survives throughout.

Scope dispatch had a cousin of the same bug: site-ness was inferred from
`bool(crawl_params)`, but site rows were created with an *empty dict* —
falsy — so site-scope bulk crawls were silently dispatched as single-page
crawls, consumed site-quota, and returned page-shaped data labeled
`succeeded`. Site rows now always carry real limit parameters
(`max_pages`, `time_budget_ms` from the server defaults), and scope is
dispatched on `crawl_params is not None` everywhere.

---

## The two journeys, end to end

Both journeys now have a terminal "customer" state, and both are fully
instrumented:

**Plan B — the self-serve pipeline (user → API power user → paying API customer)**

1. `user_registered` — an account exists.
2. `analysis_completed` / `report_exported` — they use the product by hand.
3. `gated_feature_hit` (shown, once per user) → `api_key_created` — the
   bulk gate offer renders, they cross it, one click.
4. `api_key_first_used` — the key actually works for them; the stage
   moves to `api_consumer` here. **This is not a customer**: the API is
   free. Stage and
   usage data (`api_key_usage_daily`) tell us who the power users are —
   these are the people to invite when the paid tier ships.
5. `became_customer` with `source: "api"` — a human closes the deal
   (paid API plan). Stage moves to `customer`, terminal.

**Plan A — the services pipeline (user → "I can't do this myself" → services customer)**

1. `user_registered`, `analysis_completed` — same start.
2. After ~3 completed analyses, the `services_offer` nudge appears on
   result/dashboard: "Want the fixes done for you?" — aimed at the user
   with work to do and no personnel to do it. Its CTA points to
   `/contact`.
3. `contact_mailto_clicked` — they click a mailto link (dashboard or
   contact page): a mail draft opens, deliberate intent. For known users
   this moves the stage to `inquiry`: contact intent, recorded, but not
   yet a customer.
4. `became_customer` with `source: "services"` — the deal closes offline;
   an operator records it. Stage moves to `customer`, terminal.

**Why "customer" is operator-marked, not automatic**: no in-product action
can prove a deal. A click, an export, a hundred API calls — none of them
mean money changed hands. So `became_customer` is written by a human via
the CLI (`meshweave mark-customer <email> --source api|services`), exactly
once per user, with the source and an optional note and contract value.
Runbook: from the server checkout use `uv run meshweave mark-customer …`
(the package ships `webapp`, so the installed command works); inside the
app container it is `python -m meshweave.cli mark-customer …`. The funnel
can therefore answer the only question the plan exists for:
*of the people who engaged, how many became customers — and via which
journey?*

**Anti-nagging rules**: once a user shows contact intent (`inquiry`),
gate and services offers stop rendering — we do not re-pitch someone who
already raised their hand. Once they are a `customer`, all offers stop.
An API power user can still be a Plan A prospect (an agency using the API
heavily but wanting remediation done for them): the services offer
targets `registered` and `api_consumer` stages alike.

## Conversion mechanics: gates as offers

The register-to-API journey is prompted by a single nudge panel per page,
chosen by viewer context:

- **Anonymous visitors get nothing.** The result page's own sign-in CTA is
  their prompt; a gated-feature nudge would be noise, and anonymous
  impressions would pollute the per-stage metrics.
- **Registered users without a key** see the gate offer: "Audit multiple
  domains at once — bulk audits run with a free API key." The CTA links to
  the profile's API-access section, where one click creates a key. That
  is the "gate is an offer, not a wall" design: the feature stays visible
  on the dashboard, and converting is one click away, not a dead end.
- **`api_consumer` users** see only the first-call hint, and only until
  their key is actually used — the hint renders a copy-paste `curl`
  example, and `key_first_used` check makes it disappear after the first
  real call.
- **A signed-in non-owner viewing someone's public analysis** is offered
  "save this analysis" — the save flow (see below) turns a viewer into a
  registered user with the analysis attached. Owners viewing their own
  analysis never see the save pitch (they already saved it), and the
  result-page nudge yields to the save offer when both would render (one
  prompt per page).
- **Dismissal means "not now," not "never."** Dismissals are stored with
  a timestamp and resurface after ~7 days.

Every gate offer that renders to a key-less user emits
`gated_feature_hit` **once per user per feature** — the event counts
users offered to, not impressions — and when the CTA is taken, the click
beacons back `nudge_cta_click`, which lands as a distinct
`gated_feature_taken` event, also once per user per feature. This is the
instrumentation the plan called "Track E": which gate pulls. Offered →
dismissed → taken is now a measurable sequence, not a guess.

### The save and claim flows

- **Save**: a signed-in user viewing an ownerless public analysis whose
  anonymous cookie matches the crawl's `anonymous_user_id` gets a "Save
  this analysis" offer. Taking it attaches the crawl to their account.
  The cookie is the proof of origin — it must already match; there is no
  way to claim a crawl you did not run. The match is checked
  server-side, and the `None`-cookie case is handled explicitly.
- **Claim**: similar mechanics for crawls submitted anonymously from a
  browser that later signs in.

Ownership can never be taken without proof, and the wrong person can
never be credited: both flows require the browser's anonymous id to
match the crawl's recorded anonymous id before any reattribution
happens.

---

## Contact tracking: navigation vs. intent

Two different actions, two different names — curiosity must never be
able to inflate the inquiry signal:

- **Navigation** (the footer's `/contact` link) beacons `contact_click`
  and bumps the aggregate `contact_cta_clicks_total{surface}` counter
  only. Looking at the contact page is not intent.
- **Mailto clicks** (the dashboard's "Talk to us", the contact page's
  email address) beacon `contact_mailto_click` — a mail draft opening is
  deliberate intent. Aggregate counter always; for known users also a
  `contact_mailto_clicked` funnel event with the surface as payload and
  the `inquiry` stage. This is the Plan A signal: an identified user who
  reached out.

The contact page itself stays mailto-only — no stored inquiry form. That
is a product decision, not an oversight: a form would need answers about
data retention and spam handling that have not been made. The page also
carries no view tracking: nothing downstream depends on knowing how many
people looked at it.

---

## What the funnel can (and cannot) answer now

With the data above, these questions have honest answers:

- Of registered users, how many created a key? (`api_key_created`)
- Of key creators, how many actually made a call? (`api_key_first_used`,
  exactly-once)
- Which gate offers get shown, dismissed, taken? (`gated_feature_hit`
  shown vs. `gated_feature_taken`, per feature, once per user each)
- Who showed contact intent, from which surface? (`contact_mailto_clicked`
  + stage `inquiry`)
- **Who became a customer, via which journey, at what contract value?**
  (`became_customer` with `source: api|services` — the Plan A vs Plan B
  verdict, per user, with revenue attached)
- How many users are agency-shaped? (`segment_upgraded`, driven by
  `funnel_actor_domains` distinct-domain counts)
- Which users exported reports or diffs? (`report_exported`,
  `diff_exported` — per-user, so we can ask "did the agency that contacted
  us also pull proof-of-work diffs?")
- Is API usage heavy enough to need limits? (`api_key_usage_daily`:
  calls, admitted URLs, rejected URLs — per key per day)

What it still cannot answer: anything about anonymous visitors beyond
aggregate counts — by design. If Plan A fails and Plan B wins, we will
know how people used the API. If Plan A works, we will know who to talk
to. What we will never have is a per-person history of someone who never
made an account, and that is the point.

---

## Verification

- **Tests**: 401 pass. New coverage: bulk admission outcomes, retire-and-
  cancel invariants (three-run test), SSRF rejections for internal/
  metadata targets, body-shape 400s, site-params dispatch, queue recovery
  (stranded reset, no double-crawl on terminal rows, job timeout), funnel
  counter atomicity, exactly-once key activation, viewer-aware nudge
  selection, unbranded-export assertions, usage accounting, customer
  marking (terminality, exactly-once, bad-source rejection), services-
  offer selection (threshold, inquiry suppression).
- **Static gates**: ruff (lint + format) clean, mypy clean across 85
  files, radon cyclomatic complexity zero C/D/E/F blocks.
- **Migrations**: three new Alembic revisions — the funnel tables
  (`a1b2c3d4e5f6`), queue columns on crawls (`b2c3d4e5f6a7`), usage
  accounting (`c3d4e5f6a7b8`). Chain: `4c6f9c5ed377` → `a1b2c3d4e5f6` →
  `b2c3d4e5f6a7` → `c3d4e5f6a7b8`.

## Known deliberate gaps

Deferred on purpose, recorded so future-us does not rediscover them:

- **Session-based browser access to v1**: dropped by the owner; internal
  callers still use the older session routes. Solving it is a separate
  task with its own design (likely a same-origin session→key exchange).
- **Rate limits on the bulk API**: waiting on `api_key_usage_daily` data.
- **Per-IP throttling on `/api/track` beacons**: spam skews only the
  aggregate counters, never the funnel tables; acceptable for now.
- **No bulk-submission form in the UI**: the gate points to key creation
  and API docs instead — the API is the pipeline, and a 25-URL form would
  be the same feature with worse ergonomics.

## Honest weaknesses in the current design

Things the team should keep an eye on — none block shipping, but each has
a real cost:

- **`inquiry` is still a proxy, just a better one.** Only a deliberate
  mailto click can move a user to `inquiry` now — footer navigation
  cannot inflate it — but there is still no "real inquiry" confirmation
  (a reply, a call, a scheduled meeting), so treat `inquiry` as a
  leading indicator. The honest Plan A conversion number remains
  `became_customer with source=services`.
- **`services_offer` targets a proxy, not the real signal.** "3+ analyses"
  means "active user," not "user without remediation personnel." We
  cannot detect "no personnel" from behavior; only the inquiry tells us.
  Expected noise, accepted by the team: the offer will be shown to users
  who will never need it — dismissal data will tell us how much.

## Data-interpretation history (read before querying old rows)

Corrections made 2026-09-16; earlier funnel rows reflect the old
behavior and cannot be re-attributed:

- Before the split, any contact-link click by a known user emitted
  `contact_clicked` and moved the stage to `inquiry` — including footer
  navigation. `contact_clicked` rows and `inquiry` stages earned before
  the split overcount intent; the current names are
  `contact_mailto_clicked` (intent) and counter-only `contact_click`
  (navigation).
- Before 2026-09-16 the click beacon carried no `surface`, so all
  `contact_cta_clicks_total` traffic landed under the `footer` label.
- Before the once-per-user change, `gated_feature_hit` was emitted on
  every render, so pre-change rows count impressions, not users.
