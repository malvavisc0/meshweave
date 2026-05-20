# meshweave

Render pages with Playwright, extract markdown, classify links, find emails, and optionally crawl internal links. Includes a reusable Python library and a CLI.

Package name: meshweave
Import name: meshweave
License: MIT

## Install

Install with minimal core dependencies:

```
pip install -e .
```

To enable the Playwright renderer:

```
pip install -e ".[renderer]"
playwright install --with-deps chromium
```

Note: Playwright requires a browser install (see command above).

## CLI

The CLI provides the **`crawl`** subcommand.
Running `meshweave <url>` without a subcommand automatically uses `crawl`.

### `meshweave crawl`

Render a single page and extract markdown, links, and emails:

```
meshweave crawl https://example.com
```

Crawl internal links (up to 50 pages, output to file):

```
meshweave crawl https://example.com \
  --crawl-internal --max-pages 50 \
  -o output.json
```

All flags use `--flag` / `--no-flag` toggles (no `=true` needed):

```
meshweave crawl https://example.com \
  --crawl-internal \
  --max-pages 50 \
  --same-domain \
  --include-emails \
  --deobfuscate \
  --throttle-ms 100 \
  --per-page-timeout 20.0 \
  --disable-cache \
  --cache-dir .cache \
  -o output.json
```

Run `meshweave crawl --help` for the full list of options.

### Environment variables

- `MESHWEAVE_CACHE_DIR`: directory for HTML cache (default: `/tmp/markdownify/cache`)
- `MARKDOWNIFYMESHWEAVE_DISABLE_CACHE`: when `"true"` / `"1"` / `"yes"` / `"on"`, bypass cache globally for a run

## Library usage

```python
import asyncio
from meshweave import crawl


async def main():
    payload = await crawl(
        url="https://example.com",
        crawl_internal=True,
        crawl_max_pages=25,
        same_domain_only=True,
        include_emails=True,
        deobfuscate_emails=True,
        throttle_ms=0,
        per_page_timeout=15.0,
        cache_dir=".cache",  # or None to use MESHWEAVE_CACHE_DIR / default
    )
    # payload contains:
    # - page: { title, description, og: { title, description, image, url }, canonical }
    # - markdown: formatted text
    # - links: { internal, external }
    # - metrics: { render, extraction }
    # - emails: { unique, by_url, sources (deduped found_as array), counts }
    # - crawl: { enabled, start_url, visited, limits: { max_pages }, reason_stopped }


asyncio.run(main())
```

If you want only rendering/markdown:

```python
from meshweave import (
    render_page,
    soup_from_html,
    preprocess_soup,
    to_markdown,
    extract_page_meta,
)

html, metrics = asyncio.run(render_page("https://example.com", cache_dir=".cache"))
soup = soup_from_html(html)
meta = extract_page_meta(soup)
soup = preprocess_soup(
    soup, base_url="https://example.com", final_url=metrics.final_url
)
md = to_markdown(soup)
```

## Behavior highlights

- Same-domain internal crawl with BFS until `crawl_max_pages` is reached (no depth parameter).
- Loop safety: skips root paths (`/` and absolute same-domain roots).
- Ignore patterns (applied in internal link lists and crawl queue):
  - `^/(api|auth|account|login|signup)(/|$)`
  - `^/(static|assets|cdn)/`
  - filetypes: `.(mp3|mp4|pdf|zip|png|jpg|jpeg|svg|webp|ico)`
- Emails extracted from `mailto:` and visible text with deobfuscation.
- `emails.by_url` includes only pages containing emails.
- `emails.sources` is deduplicated per (email, url) with `found_as` array.
- Markdown is cleaned (nav/footer/header/aside, cookie banners, HTML comments stripped) and spacing normalized.
- Page metadata (title, description, og, canonical) exposed at `payload.page`.

## Web App (FastAPI UI)

A modern web UI is included under `webapp/` for submitting URLs and viewing results.

Run the web app:

```
uvicorn webapp.main:app --host 0.0.0.0 --port 8080
```

Environment:
- `SQLITE_PATH` (default: `/db/app.db`) controls the SQLite DB location used by the web UI.
- The web UI requires the renderer extras (Playwright) at runtime to render pages.
- `APP_VERSION` (optional): value displayed in the site footer, e.g. "1.3.2".
- `FOOTER_REPO_URL` (optional, default: `https://github.com/malvavisc0/meshweave`): Open Source link in the footer.
- `FOOTER_CONTACT_EMAIL` (optional, default: `hello@acme.com`): Contact email used in the footer and legal pages.
- `FOOTER_PRIVACY_URL` (optional, default: `/privacy`): URL/path for the Privacy link in the footer.
- `FOOTER_TERMS_URL` (optional, default: `/terms`): URL/path for the Terms link in the footer.
- `CLAIM_PUBLIC_MIN_AGE_HOURS` (optional, default: `24`): Minimum age in hours before a public, ownerless analysis becomes claimable by a logged-in user.

### Key Features
- **Submission**: Form on homepage to input URL, options for public/private, crawl depth, email inclusion.
- **Progress Tracking**: Real-time updates for pending/running jobs, with cancel/retry.
- **Results Display**:
  - Quick stats: HTTP status, load time, bytes, requests, email count, pages (flat cards).
  - Social/Claim panels (public only).
  - Collapsible Pages section: Filter by emails/length, search, select for inclusion; view outline and markdown (accordion for desktop/mobile).
  - Collapsible Emails table: Filter by type/domain, add to prospects, manage leads (accordion).
  - Mobile tabs for stats, leads, summary, chat.
- **Compose & Chat** (AI Integration, Flat/Slack-Inspired Design):
  - Tabbed interface: "Structured" (vertical form for product/tone/CTA/length/shorten, buttons for Sales Pitch, Outreach Email, Weaknesses & Opportunities, Clarity Assessment) vs. "Free Chat" (page selector, message bubbles, input bar).
  - Flat design: Solid colors (#007bff primary), rounded corners (4px), subtle hovers (opacity 0.8), no shadows; Slack-like chat bubbles (light bg for AI, blue for user, border-left accent).
  - Results: Shared textarea for generated content with copy/download; modal preview on mobile.
- **Prospects**: Save sites/emails to personal list, manage shortlists.
- **Sharing/Security**: Public shares via X/LinkedIn, CSRF protection, rate limiting, noindex for private.

Public vs Private submissions:
- Public submissions are deduplicated by canonical URL (domain + path + query) and are accessible via a short key.
- Private submissions always create a new record and are accessible via a UUID page (not listed, marked noindex).

Canonicalization rules:
- Domain: lowercase, strip leading `www.` only (keep other subdomains).
- Path: ensure leading `/`, normalize empty to `/`, trim trailing `/` except for root `/`. Case is preserved.
- Query: parse with `parse_qsl(..., keep_blank_values=True)`, sort by `(key, value)`, rebuild with `urlencode(doseq=True)`. Duplicates and blanks preserved. Fragment `#...` is ignored.
- Canonical URL for display: `https://{domain}{path}{?query}`.

Access patterns (HTML):
- Public by key: `GET /analysis/public/{key}`
- Private by ID: `GET /analysis/private/{crawl_id}`
- Domain index (list of public entries for a domain): `GET /domain/{domain}`

Access patterns (JSON):
- Public by key: `GET /api/analysis/public/{key}`
- Private by ID: `GET /api/analysis/private/{crawl_id}`
- Domain index: `GET /api/domain/{domain}`

Keys:
- Short key for public pages is a URL-safe Base64 (no padding) encoding of random UUID4 bytes (~22 chars).
- Keys are random identifiers (not derived from the URL) and are stable for a given canonical URL due to deduplication on public upserts.

Deduplication behavior:
- Public: upsert by `(visibility='public', domain, path, query)` so repeated submissions for the same canonical URL reuse the same key/row and trigger a refresh.
- Private: no upsert; each submission is a new record with its own UUID page.

Security:
- Basic CSRF tokening, simple rate limiting, and optional request metadata logging are implemented in the web UI.

## Authentication & Crawl Site Spec

See the unified spec at:
- docs/auth.md

## Development

- Editable install:
```
pip install -e ".[renderer]"
playwright install --with-deps chromium
```

- Run the CLI:
```
meshweave crawl https://example.com --crawl-internal --max-pages 10
```

- Environment variables:
```
export MESHWEAVE_CACHE_DIR=.cache
export MARKDOWNIFYMESHWEAVE_DISABLE_CACHE=true
```

## License

MIT


## Testing

- Run tests:
  - pytest -q
- The test suite avoids launching a real browser:
  - A lightweight Playwright shim is provided in [tests/conftest.py](tests/conftest.py) so imports of [fetcher.get_rendered_html()](meshweave/fetcher.py) work without installing browsers.
- Relevant tests:
  - Cache key determinism: [tests/test_fetcher_cache.py](tests/test_fetcher_cache.py)
  - Link classification and root-path skip: [tests/test_links.py](tests/test_links.py)
  - Email extraction deobfuscation and script/style exclusion: [tests/test_emails.py](tests/test_emails.py)

## Security and behavior defaults

- TLS verification is ON by default:
  - [fetcher.get_rendered_html()](meshweave/fetcher.py) parameter ignore_https_errors defaults to False. Pages with invalid TLS will fail fast.
- Resource blocking is reliable:
  - Requests are blocked by resource type via a unified route in [fetcher.get_rendered_html()](meshweave/fetcher.py) (e.g., "image", "stylesheet", "font", "media").
- Viewport is applied consistently:
  - The effective viewport is selected with [_select_viewport()](meshweave/fetcher.py) and set on the browser context in [fetcher.get_rendered_html()](meshweave/fetcher.py).
- Link classification clarifications:
  - Root path "/" is skipped intentionally; internal links only include meaningful paths, via [_classify_links()](meshweave/core.py).

## Cache controls and semantics

- Expanded cache keys ensure deterministic behavior:
  - Cache keys now include effective viewport, resolved user agent, wait flags, headers, referer, TLS flag, resource blocking, stealth, and more, all within [fetcher.get_rendered_html()](meshweave/fetcher.py).
- When a custom intercept_requests handler is provided, caching is bypassed (callables are not hashed).
- CLI:
  - --disable-cache flag added; see [cli.main_crawl()](meshweave/cli.py).
- Environment variables:
  - MESHWEAVE_CACHE_DIR: HTML cache directory (default: /tmp/markdownify/cache)
  - MARKDOWNIFYMESHWEAVE_DISABLE_CACHE: when "true"/"1"/"yes"/"on", bypass cache globally for a run
  - MARKDOWNIFY_DEBUG_SLOWMO_MS: when set (e.g., "50"), adds Playwright slow_mo for debugging only
