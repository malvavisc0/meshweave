# MeshWeave: AI Agent Visibility Audit

## What MeshWeave Does

MeshWeave helps teams understand what AI agents can make of their website.
Someone may be able to read a site and immediately understand the product,
the audience, the proof, and the next step. An AI agent may not. It may miss an
important page, fail to connect a product to its company, overlook a contact
path, or lack enough clear evidence to recommend the brand.

MeshWeave finds those gaps. It crawls a site as a machine reader would, checks
the signals available to an agent, and turns the findings into a practical list
of improvements. The goal is not another abstract marketing score. The goal is
to answer three useful questions:

- Can an AI agent find and extract the answer a buyer needs?
- Does the site give an agent enough consistent evidence to trust and recommend
  the business?
- Can an agent understand the offer and find a credible next step?

The result is a clear baseline for a site before a launch, after a redesign, or
while a team is improving its AI-facing content.

## Why This Matters

Search is no longer limited to a list of links. Buyers increasingly ask AI
agents to explain a category, compare vendors, summarize options, or suggest
what to do next. In that setting, being technically present is not enough. The
site also needs to be easy to interpret.

Traditional SEO remains important for being found in search. MeshWeave looks at
the layer after discovery: whether an AI agent can accurately understand,
extract, trust, cite, and use what the site says.

This distinction matters because a site can have good search fundamentals and
still be difficult for an agent to use. A long marketing paragraph may hide the
answer. A missing `Organization` schema may leave the business identity
ambiguous. A blocked crawler may never reach the page. An email buried on a
legal page may not look like a useful next step.

## What A Report Tells You

Each audit combines observable website evidence with clear explanations of what
that evidence means. A report can show:

- Which pages were rendered and evaluated
- How well the site uses structured data and page hierarchy
- Whether important content is deep enough and fresh enough to be useful
- Whether AI crawlers can access the site
- Whether the brand, product, and descriptions stay consistent across pages
- Whether the homepage and metadata explain the offer clearly
- Whether an agent can identify a credible contact or next step
- Which fixes should be handled first

Recommendations are tied to findings rather than generic checklists. For
example, a report might say that 9 of 10 images lack alt text, that only one
same-domain contact was found, or that the site has complete schema coverage but
weak content depth. That gives a team something specific to fix and something
measurable to check on the next run.

Each fix recommendation also carries a model-derived impact estimate:
`expected_points` is computed by re-evaluating the lens composite with the
fixed factor score, using the same weights, renormalization, and calibration
curve as the real score — so the predicted delta is checkable against the
observed one on the next run (the diff page shows predicted vs observed for
every resolved finding). Recommendation ordering follows these computed points
rather than typed-in ranges.

## The Three Lenses

MeshWeave separates three different failure modes. Keeping them separate makes
the report easier to act on: the fix for a missing answer is not the same as the
fix for an unclear company identity or a missing contact path.

### AEO: Can the Agent Extract an Answer?

Answer Engine Optimization measures whether content is structured so an agent
can find and reuse a clear answer. It looks at:

- JSON-LD and schema coverage
- Heading hierarchy and single-H1 structure
- Lists, tables, paragraphs, word count, and image alt text
- Published and modified dates
- Optional human observations about query match, voice results, and capture
  rate

**A low AEO result usually means:** the answer may be present, but it is hard to
locate, interpret, or quote cleanly.

**A useful response is:** make important answers more direct, organize pages
with meaningful headings, add the right structured data, and remove ambiguity.

AEO ratings are `Poor`, `Below Average`, `Average`, `Strong`, and `Excellent`.

### GEO: Does the Site Look Credible and Consistent?

Generative Engine Optimization measures the evidence an AI agent can use when it
decides whether a business belongs in a recommendation. It looks at:

- Organization and other structured-data coverage
- Consistent names and descriptions across pages
- Author, review, contact, privacy, terms, and video signals
- Access for GPTBot, ClaudeBot, PerplexityBot, and other declared crawlers
- `robots.txt`, XML sitemaps, `llms.txt`, and `llms-full.txt`
- Content depth and the number of connected external identity profiles
- Optional human observation of citation frequency in LLM products

A simulated citation check runs alongside the AAX analysis: buyer questions are
generated for the site's category, then an LLM answers each using only the
crawled pages, and the result reports how often the brand was mentioned and
which pages were cited. It is a grounded simulation of content extractability —
labeled as simulated, kept separate from the manual citation input, and
comparable across revisions so it serves as before/after evidence for the
re-check loop.

**A low GEO result usually means:** the site does not provide enough consistent,
credible, or accessible evidence for an agent to confidently represent it.

**A useful response is:** clarify the organization identity, publish supporting
proof, keep descriptions aligned, and make legitimate AI crawler access explicit.

GEO ratings are `Invisible`, `Emerging`, `Visible`, `Authoritative`, and
`Dominant`.

### AAX: Can the Agent Understand the Offer and Take the Next Step?

AI Agent Experience focuses on comprehension and usability from the content an
agent can read. It is not an interactive browser-agent test. It does not submit
forms, check out, log in, or complete a transaction.

AAX evaluates:

- **Homepage comprehension:** Can the agent identify the brand, product,
  audience, features, and call to action?
- **Meta optimization:** Do the title, description, Open Graph data, canonical,
  Twitter metadata, and JSON-LD describe the page well?
- **Content delta:** Do multiple pages agree about the company, product,
  pricing, audience, and strengths?
- **`llms.txt` (small weight):** Are the optional agent guidance files
  present? Scored lightly — an emerging convention most AI crawlers do
  not yet consume, and already credited inside the GEO crawl-access
  factor, so it must not dominate the agent-experience verdict
- **Email validation:** Are useful contact addresses present and credible?
- **Contactability:** Can a visitor or agent find a practical way forward?

**A low AAX result usually means:** the site may be readable page by page, but
the overall offer, relationships, or next step remains unclear.

**A useful response is:** state what the business does, who it is for, what it
offers, how it differs, and where a qualified visitor should go next.

AAX ratings are `Opaque`, `Unclear`, `Readable`, `Clear`, and `Fluent`.

## How Scoring Works

Scores are diagnostic signals, not promises of rankings, citations, traffic, or
revenue. They are designed to make progress visible and to help a team decide
what to do next.

Each lens uses weighted factors. Some factors can be measured from a crawl;
others need human observation. If a factor is unavailable, it is left out and
the remaining weights are rebalanced. This prevents a missing date or an
unfilled manual input from being treated as a failing website signal.

The shared calculation is:

```text
weighted score = sum(score * weight) / sum(available weights)
final score = 100 * (weighted score / 100) ** 1.15
```

The curve gently compresses inflated upper-range scores. For example, a raw 80
becomes about 77 and a raw 70 becomes about 66. Final values are capped at 100
and rounded to one decimal place.

Every AEO and GEO result includes an auto-only score as well as the full score.
That distinction tells the reader what the website itself demonstrated versus
what still depends on manual research. AAX is added when the LLM analysis is
enabled and completes successfully.

## What Is Automatic and What Needs a Person?

The crawler can reliably inspect page structure, metadata, JSON-LD, links,
content volume, dates, crawler rules, sitemaps, contact signals, and cross-page
consistency. It cannot know from one crawl how often a real query produces a
citation or voice answer.

The following inputs are therefore manual:

- **Capture rate:** how often target queries return the site in snippets or AI
  Overviews
- **Query match:** how closely the content answers the questions customers ask
- **Voice rate:** how often voice assistants select the site as the answer
- **Citation:** how frequently LLM products mention or link to the brand

Those inputs are not hidden inside an automated guess. The report labels them as
manual and keeps them separate from crawl evidence.

## A Typical Audit

1. A user submits a URL, either anonymously for a public analysis or while
   signed in for a private report and history.
2. MeshWeave renders the starting page through LightPanda, including pages that
   need JavaScript to display their content.
3. It converts the page to machine-readable Markdown and extracts metadata,
   headings, links, JSON-LD, content metrics, crawler rules, sitemaps, and
   contact information.
4. It follows in-scope internal links breadth-first within the page, depth, and
   time limits. Sitemap URLs can seed discovery, but infrastructure files such
   as `robots.txt` are not scored as content pages.
5. It detects bot-protection interstitials and refusal statuses rather than
   scoring the blocker as if it were the site. Blocked subpages are recorded and
   excluded from content averages.
6. AEO and GEO scores are calculated immediately, with factor details and
   recommendations.
7. When enabled, AAX runs asynchronously. Its durable database queue survives
   a worker restart and updates the report when the analysis finishes.

## Example Interpretation

Imagine a site with complete schema coverage and a clear homepage, but only one
of ten images has alt text, an average of 600 words per page, and no customer
proof beyond an Organization schema. The report should not simply call the site
“good” or “bad.” It should explain the shape of the result:

- AEO may be strong because the site is well structured and richly marked up.
- GEO may be only visible because authority and external proof are limited.
- AAX may depend on whether the offer, pricing, and contact path remain clear
  across the deeper pages.

That is the practical value of separate lenses. A team can preserve what is
working while addressing the weakest evidence instead of rewriting everything.

## Web Application

The FastAPI web application adds persistence and collaboration features around
the crawler:

- Public analysis pages and a browseable gallery
- Private reports for authenticated users
- Google OAuth for higher quotas and saved history
- Crawl progress and AAX status updates
- Side-by-side revision comparisons
- Per-domain score history with AEO, GEO, and AAX deltas
- CSV history export
- Authenticated prospects and contact records
- A re-check flow on the report page: signed lens deltas against the previous
  run, a one-click rerun that preserves the old revision, and a findings diff
  showing each resolved fix's predicted versus observed score change

Public previews are intentionally limited to headline scores, ratings, counts,
and a high-level risk summary. They do not expose raw emails, full page bodies,
recommendation guidance, or the complete score snapshot. Owners can access the
full private payload, which is marked `noindex`.

## Security and Privacy

MeshWeave treats submitted URLs and extracted contact information as sensitive
operational data.

- Anonymous and authenticated crawls have separate server-side limits.
- Google OAuth protects private reports and saved history.
- Session lifetime, login rate limits, and cookie behavior are configurable.
- State-changing operations use HMAC-bound CSRF tokens.
- Non-owners receive a not-found response for private resources, avoiding UUID
  enumeration.
- Production configuration requires a secret key, database credentials, OAuth
  credentials, and LLM settings.
- Telemetry, error reporting, and Langfuse tracing are opt-in.

Default Compose limits are 10 pages for anonymous crawls and 25 pages for
authenticated crawls. Authenticated runs can be capped at 250 pages and depth
3; operators can change these values through environment configuration.

## Reliability and Operations

The service exposes three operational endpoints:

- `/healthz` confirms that the process is alive.
- `/readyz` checks database connectivity and, when configured, OAuth readiness.
- `/metrics` exposes Prometheus-compatible application metrics.

The AAX worker polls its PostgreSQL-backed queue every five seconds. Jobs are
claimed atomically so concurrent workers do not process the same crawl. A job
running for more than 30 minutes is reset for retry after a worker or container
failure.

## Technology, for Technical Readers

- **Application:** FastAPI and Uvicorn
- **Crawler:** Playwright connected to a LightPanda CDP browser
- **Extraction:** Beautiful Soup, lxml, HTML-to-Markdown, and custom Python
  audits
- **Scoring:** Pure Python AEO, GEO, and AAX scoring modules
- **AI evaluation:** PydanticAI with an OpenAI-compatible LLM endpoint and
  structured output validation
- **Persistence:** PostgreSQL with SQLAlchemy and Alembic migrations
- **Queueing:** Durable AAX state in PostgreSQL — the queue is the
  database; no separate broker is involved
- **Observability:** Prometheus metrics and optional Langfuse traces
- **Runtime:** Python 3.14+ managed with `uv`

## Interfaces

### Web

The local Compose stack runs the webapp at `http://localhost:8080`. It includes
PostgreSQL, LightPanda, and the application with source mounts and
Uvicorn reload support.

### CLI

```bash
meshweave crawl https://example.com --max-pages 25 --max-depth 1 -o output.json
```

The CLI requires `MESHWEAVE_CDP_ENDPOINT`, always follows in-scope internal
links, and can write both a JSON payload and one Markdown file per page. The
same crawler is available through the async `meshweave.crawl()` library entry
point.

### JSON API and Agent Integration

The web application exposes a free JSON API with public preview endpoints that do
not require a key:

- `POST /submit` starts an analysis (anonymous runs are public, capped at 10
  pages).
- `GET /api/status/{crawl_id}` polls crawl progress.
- `GET /api/analysis/public/{key}` returns the curated public preview:
  headline scores, ratings, a risk summary, and counts.
- `GET /api/analysis/public/{key}/summary` returns the short verdict only.
- `GET /api/domain/{domain}` lists public analyses for a domain.

Full private payloads — complete factors, recommendations, and score snapshots —
require a browser-authenticated owner session or an API key belonging to the
analysis owner. Users create and revoke keys from their profile page; the secret is
shown only once.

The site also serves the agent-facing files it audits for:
`/.well-known/llms.txt` and `/.well-known/llms-full.txt` describe the product
and the API to AI agents, and `robots.txt` declares AI crawlers welcome.

### Production

The production Compose stack uses a published image, persistent PostgreSQL,
secure cookies, required secrets, and debug mode
disabled. A reverse proxy is expected to provide the public HTTPS boundary.

## The Practical Takeaway

MeshWeave gives teams a way to see their website from an AI agent's point of
view without pretending that one score can predict the entire market. It shows
what the crawl can verify, identifies where the story becomes unclear, and
provides a prioritized route from “the page exists” to “an agent can understand
and use it.”
