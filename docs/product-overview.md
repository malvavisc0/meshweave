# Product Overview — markdownify-crawler

An open-source stack to render and analyze websites, extract clean Markdown, classify links, detect emails, and optionally crawl internal pages—packaged as a Python library, CLI, optional API server, and a modern web app with AI-assisted analysis and lightweight lead capture.

License: MIT


## 1) Summary

- Offering: End-to-end website rendering, content extraction, analysis, and light lead discovery stack provided as a Python library, CLI, optional API, and modern web app with AI assistance.
- Core value: Turn any website into clean Markdown, link/email intelligence, and actionable insights without building or maintaining a crawler stack.
- Primary users:
  - Sales/BDR/Partnerships
  - Growth/SEO/Content strategists
  - Founders/Analysts
  - Developers/Researchers


## 2) Problems We Solve

- Prospecting and outreach: Finding contacts and understanding target sites is slow; context is scattered.
- Competitive and content research: Extracting structured text and link maps typically requires fragile custom scrapers.
- JS-heavy sites: Headless rendering and resource management are complex to set up and stabilize.
- Collaboration and sharing: Non-technical teammates need a simple UI and shareable links to results.
- Developer ergonomics: Teams want a reusable library/CLI/API with deterministic caching, sensible defaults, and tests.


## 3) Who It Helps (Personas)

- Sales/BDR:
  - Detect and preview emails with sources; export CSV; save domains/contacts to Prospects.
  - Generate outreach drafts with AI grounded in selected page content.
- Growth/SEO/Content:
  - Inventory internal/external link profiles; extract clean Markdown; review top external domains.
  - Generate messaging drafts and clarity assessments per page.
- Founders/Analysts:
  - Run quick competitor or customer scans with shareable public pages; track page-level stats like HTTP status, load time, bytes, and request counts.
- Developers/Researchers:
  - Programmatic access via library, CLI, and optional API with deterministic caching and security defaults.


## 4) Capabilities by Component

### A) Python Library
- Rendering and extraction:
  - Playwright-based rendering with viewport control; TLS verification on by default.
  - Resource blocking for speed and determinism; behavior in [fetcher.get_rendered_html()](markdownify_crawler/fetcher.py:475).
  - BeautifulSoup cleaning and Markdown conversion; page metadata extraction.
- Link classification and BFS:
  - Internal vs external link classification; BFS crawl with a page budget and ignore rules.
- Email detection:
  - Extract from mailto and visible text with deobfuscation; sources tracked per URL.
- Deterministic caching:
  - Cache keys include rendering parameters; details in [fetcher.get_rendered_html()](markdownify_crawler/fetcher.py:391).
- Selected internals:
  - Viewport selection: [_select_viewport()](markdownify_crawler/fetcher.py:165)
  - TLS strict by default: [fetcher.get_rendered_html()](markdownify_crawler/fetcher.py:275)

### B) CLI
- Crawl a single URL or BFS internal links up to a page budget.
- Configure throttling, timeouts, caching, same-domain policy, email extraction, and deobfuscation.
- Emits structured JSON with page meta, markdown, links, metrics, emails, and crawl info.

### C) FastAPI Crawler API (Optional)
- HTTP endpoint mirrors the library crawl for one-shot programmatic usage: [markdownify_crawler.server.crawl_endpoint()](markdownify_crawler/server.py:10)
- Not required by the web app (see Operations Note).

### D) Web App
- Unified submission:
  - Page and Site modes via a single POST handler [webapp.routers.submissions.submit()](webapp/routers/submissions.py:30).
  - Public submissions dedupe on canonicalized root and run a site-scope crawl; private page crawls are owner-restricted.
  - CSRF, origin/referer checks, basic rate limiting, and session rotation on submit.
- Progress:
  - Real-time status for pending/running jobs; cancel/retry for owners; JSON progress APIs in [webapp.routers.progress](webapp/routers/progress.py).
- Results UI:
  - Quick stats: HTTP status, load time, bytes, network requests, emails, internal/external counts.
  - Pages panel: search/filter/select pages; view Markdown; copy/download content.
  - Emails panel: filter by domain and detection type; export CSV; add/edit leads; gated preview for anonymous public views.
  - Social and Links panels; public share links; domain index pages.
- AI: Compose & Chat (owner-only):
  - Structured actions like Sales Pitch, Outreach Email, Weaknesses and Opportunities grounded in selected pages and saved Products.
  - Streaming chat with persisted history; routes under [webapp.routers.ai](webapp/routers/ai.py).
- Prospects mini-CRM:
  - Save domains as Prospects; manage contacts, status, tags, notes; export contacts CSV; APIs in [webapp.routers.prospects](webapp/routers/prospects.py) and page in [webapp.routers.prospects_page](webapp/routers/prospects_page.py).
- Products:
  - Store product defaults to drive structured AI outputs; page in [webapp.routers.products](webapp/routers/products.py).
- Public directories and domain index:
  - Public analyses by short key; domain index; JSON mirrors in [webapp.routers.api](webapp/routers/api.py).


## 5) How It Works (Workflow and Data Flow)

1) Submission
- Single endpoint branches by mode and visibility; see [webapp.routers.submissions.submit()](webapp/routers/submissions.py:30).
- Public: upsert on canonical root and run site-scope crawl for richer results and shareability.
- Private: single-page owner-restricted crawls by default.

2) Crawling
- Page mode: background [webapp.services.crawling.run_crawl_task()](webapp/services/crawling.py:15) delegates to the library crawl.
- Site mode: BFS [webapp.services.site_crawling.run_site_crawl_task()](webapp/services/site_crawling.py:109) uses the library’s render and extract primitives; enforces max pages, depth, and time budget; persists per-URL details.

3) Persistence
- Links and emails are persisted per page with dedup/aggregation; [webapp.services.persist.persist_page()](webapp/services/persist.py:41) and cleanup via [webapp.services.persist.clear_crawl_data()](webapp/services/persist.py:28).

4) Access and UI
- Owners see My Jobs; can cancel/retry, chat, and manage leads and prospects.
- Anonymous users can view public analyses with gated email previews and later claim public analyses after a minimum age.


## 6) Security, Privacy, Reliability

- Security:
  - CSRF enforcement via middleware and token checks; origin/referer validation; basic IP/session rate limiting; session rotation on submit.
  - Owner-only enforcement for private results and AI chat.
- Privacy:
  - Private results are noindex and owner-restricted; public results are shareable by key with gated email viewing for anonymous users.
- Reliability:
  - Deterministic cache keys, explicit resource blocking, and error handling in render/extract pipeline.
  - Health and readiness endpoints and Prometheus metrics in [webapp.routers.api](webapp/routers/api.py).


## 7) Differentiators (Unique Value)

- End-to-end for devs and non‑devs:
  - Library + CLI + optional API + polished web UI; quick time-to-value without bespoke scrapers.
- Clean Markdown extraction:
  - Opinionated cleaning yields readable, analysis-ready Markdown beyond raw HTML scraping.
- Lead discovery + mini-CRM:
  - Emails tied to their sources and integrated with Prospect/Contact management; CSV export fits existing outbound tools.
- AI assistance grounded in real content:
  - Structured actions and free chat over selected pages; persisted threads; streaming UX.
- Public shareability with canonicalization:
  - Public analyses dedupe by canonical root, reducing noise; claim flow to attach ownership when needed.
- Developer ergonomics:
  - Deterministic caching, security defaults, test coverage, and env-driven behavior.


## 8) Constraints and Assumptions

- Playwright dependency: Chromium install required.
- Site crawl BFS with max_pages, max_depth, and time budget; fragments ignored during canonicalization.
- AI requires provider configuration and API key; strict limits applied.
- Anonymous submissions default to public; authenticated submissions default to private in UI flows.
- Behavior is environment-driven across security, logging, limits, and footer config.


## 9) Quick Reference

- Web App:
  - Run with uvicorn entrypoint [webapp.main.app](webapp/main.py) via factory [webapp.app.create_app()](webapp/app.py:207).
  - Key endpoints: public/private analysis, domain index, [webapp.routers.api.readyz()](webapp/routers/api.py:641), [webapp.routers.api.metrics()](webapp/routers/api.py:670).
- CLI:
  - Single URL or BFS crawl with options; examples in [README.md](README.md).
- Optional Crawler API:
  - Programmatic endpoint [markdownify_crawler.server.crawl_endpoint()](markdownify_crawler/server.py:10).


## 10) Operations Note: Docker Compose Crawler Service (Optional)

- The web app does not require a separate crawler container. It calls the library directly in background tasks:
  - Page: [webapp.services.crawling.run_crawl_task()](webapp/services/crawling.py:15).
  - Site: [webapp.services.site_crawling.run_site_crawl_task()](webapp/services/site_crawling.py:109).
- The service named crawler in [docker-compose.yaml](docker-compose.yaml) is optional for this project. It can be removed to simplify the stack while keeping postgres, redis, and the web app.
- Keep it only if an external integration needs an HTTP crawl endpoint; test with [markdownify_crawler.server.crawl_endpoint()](markdownify_crawler/server.py:10).


## 11) Mermaid: Request to Insight Flow

flowchart TD
  A[User submits URL on web UI] --> B[Router submissions.py saves Crawl row]
  B --> C[Background task chooses page or site]
  C --> D[Page run_crawl_task uses library crawl]
  C --> E[Site run_site_crawl_task uses render and classify]
  D --> F[persist_page stores links and emails]
  E --> F
  F --> G[Payload JSON stored on Crawl row]
  G --> H[Results view renders metrics pages emails]
  H --> I[AI Compose and Chat uses selected content]
  H --> J[Prospects and Contacts export CSV]


## 12) Inputs for the One-Line Pitch (for later)

- Project Name: markdownify-crawler
- Offering: End-to-end site analysis and light lead discovery across library, CLI, optional API, and web UI with AI
- Audience: Sales/BDR, Growth/SEO/Content, Founders/Analysts, Developers/Researchers
- Problem: Quickly turn websites into structured content, link/email intelligence, and shareable insights
- Unique Value: Clean Markdown extraction; integrated leads; AI grounded in actual page content; shareable public analyses; open-source with programmatic interfaces


## Audit Changelog

- Clarified that the web app calls the library directly and does not require the crawler container; added an explicit Operations Note.
- Tightened value statements and persona benefits to map precisely to implemented features and endpoints.
- Verified and linked core code paths: [webapp.routers.submissions.submit()](webapp/routers/submissions.py:30), [webapp.services.crawling.run_crawl_task()](webapp/services/crawling.py:15), [webapp.services.site_crawling.run_site_crawl_task()](webapp/services/site_crawling.py:109), [webapp.services.persist.persist_page()](webapp/services/persist.py:41).
- Normalized language around canonicalization, public keys, and deduplication; aligned with API routes in [webapp.routers.api](webapp/routers/api.py).
- Added Mermaid flow to visualize lifecycle from submit to insights.
- Kept license info and Quick Reference in line with current behavior.
