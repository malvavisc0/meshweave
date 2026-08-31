# MeshWeave

> See how AI reads your website — and fix what matters first.

MeshWeave checks how AI tools — answer engines (Google AI Overviews, ChatGPT), chatbots, and autonomous agents — read your website. It finds the specific problems that make your brand easy to miss or misquote, and gives you a clear list of what to fix first.

## Who this is for

- **SEO and content teams** who want to know why their brand isn't showing up in AI answers
- **Marketing and growth teams** protecting brand visibility as discovery shifts to chat and agents
- **Founders and operators** who own the numbers that depend on being found
- **Agencies and consultants** reporting to clients on how AI sees their sites
- **Product and web teams** responsible for site structure and how machines read it

## What you get

- **A clear read on how AI sees your site today** — what it extracts, cites, and recommends, plus where it gets things wrong
- **A prioritized fix list ranked by business impact** — so you start with the problems that cost you the most
- **A baseline to track over time** — catch visibility regressions before they turn into lost citations and clicks
- **Findings your whole team can act on** — plain-language, not a black box only the tools team understands

## Three Risk Lenses

MeshWeave looks at AI visibility through three distinct lenses, each measuring a different way AI can fail your brand.

| Lens | What It Measures | Business Risk |
|------|-----------------|---------------|
| **AEO** — Answer Engine Optimization | Can AI pull a clear, trustworthy answer from your content? | Your brand gets skipped — a competitor answers instead. |
| **GEO** — Generative Engine Optimization | Does AI see your brand as authoritative and trustworthy? | Your brand gets left out of recommendations. |
| **AAX** — AI Agent Experience | Can agents navigate your site and do what visitors need? | Buyers who arrive through agents give up before buying. |

## Quick Start

### Docker Compose (recommended)

```bash
cp .env.example .env   # configure OAuth, LLM, base URL
docker compose up -d
```

The webapp is available at `http://localhost:8080`.

The local Compose configuration enables authentication and requires Google OAuth
credentials for the `/readyz` healthcheck. Set `OAUTH_CLIENT_ID` and
`OAUTH_CLIENT_SECRET` in `.env` before starting the stack. AAX also requires the
LLM settings; set `AAX_ENABLED=false` if AAX is not configured locally.

### Local development

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run uvicorn webapp.main:app --host 0.0.0.0 --port 8080 --reload
```

Pages are rendered through a remote CDP browser (LightPanda). Set
`MESHWEAVE_CDP_ENDPOINT` before crawling locally:

```bash
docker run -d --name lightpanda -p 9222:9222 \
  lightpanda/browser:nightly lightpanda serve --host 0.0.0.0 --port 9222
export MESHWEAVE_CDP_ENDPOINT=http://localhost:9222
```

(Docker Compose already wires this up via the `lightpanda` service.)

## Web App

A full-featured FastAPI web application for submitting URLs and viewing AI visibility analysis results.

### Key capabilities

- **Site & page analysis** — Submit URLs for crawling with configurable depth and page limits
- **AEO / GEO / AAX scoring** — Three risk lenses, each with a score and a plain-language breakdown of what drove it
- **Real-time progress** — Live status updates for running crawls
- **Public & private results** — Anonymous runs are public; authenticated users get private reports with shareable URLs
- **Google OAuth** — Sign in for higher quotas, private results, and persistent history
- **Browse & compare** — Public analysis gallery sorted by recency, domain, or score

### Architecture

```
FastAPI (webapp/) ── PostgreSQL 18 ── Redis ── LightPanda (CDP browser)
                                                    │
                                             LLM scoring engine
                                          (OpenAI-compatible API)
```

### Key environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | — |
| `REDIS_URL` | Redis connection string | `redis://redis:6379` |
| `SITE_BASE_URL` | Public base URL for the site | — |
| `SITE_NAME` | Brand name shown in UI | `MeshWeave` |
| `OAUTH_CLIENT_ID` | Google OAuth client ID | — |
| `OAUTH_CLIENT_SECRET` | Google OAuth client secret | — |
| `LLM_BASE_URL` | OpenAI-compatible LLM endpoint | — |
| `LLM_API_KEY` | LLM API key | — |
| `LLM_MODEL` | Model name for scoring | — |
| `AAX_ENABLED` | Enable AAX scoring lens | — |
| `MESHWEAVE_CDP_ENDPOINT` | CDP browser endpoint (required for rendering) | — |
| `MESHWEAVE_CACHE_DIR` | HTML cache directory | `/tmp/meshweave/cache` |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | Langfuse credentials for AAX LLM tracing (opt-in, see [docs/langfuse.md](docs/langfuse.md)) | — |
| `FOOTER_CONTACT_EMAIL` | Contact email in footer/legal | `hello@meshweaveai.com` |

See `docker-compose.yaml` for the full set of configuration options.

The webapp defaults to 25 pages and depth 1 for authenticated site crawls, and
10 pages and depth 1 for anonymous crawls. Limits are capped separately by the
`AUTH_SITE_*` and `ANON_SITE_*` environment variables in Compose. Successful
crawls run AAX asynchronously when `AAX_ENABLED=true`; the result page displays
the crawl result while AAX is pending and adds the AAX score when analysis
finishes.

## CLI

The CLI provides the `crawl` subcommand. Running `meshweave <url>` without a subcommand defaults to `crawl`. Internal links within the URL's path scope are always crawled (`--max-pages` defaults to 25). `--output/-o` is required. Requires `MESHWEAVE_CDP_ENDPOINT` to be set — the CLI exits early otherwise.

```bash
# Site crawl within the URL's path scope (default: up to 25 pages)
meshweave crawl https://example.com -o output.json

# Larger crawl with link depth control
meshweave crawl https://example.com \
  --max-pages 50 --max-depth 2 \
  -o output.json

# Markdown output per page
meshweave crawl https://example.com \
  --max-pages 10 \
  --output-dir ./data/output \
  -o output.json
```

Run `meshweave crawl --help` for all options.

## Library Usage

```python
import asyncio
from meshweave import crawl


async def main():
    payload = await crawl(
        url="https://example.com",
        crawl_max_pages=25,
        max_depth=1,
        include_emails=True,
        deobfuscate_emails=True,
    )
    # payload contains: page, markdown, links, metrics,
    # emails, crawl info, audit, headings, and more


asyncio.run(main())
```

## Development

```bash
# Install with dev dependencies
uv sync

# Run tests
uv run pytest -q

# Run with hot-reload
uv run uvicorn webapp.main:app --host 0.0.0.0 --port 8080 --reload

# Database migrations
uv run alembic upgrade head
```

### Pre-commit hooks

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

## Documentation

- [Product Overview](docs/product-overview.md)
- [Scoring Reference](docs/scoring-reference.md)
- [Style Guide](docs/style-guide.md)
- [Observability](docs/observability.md)
- [LLM Observability (Langfuse)](docs/langfuse.md)

## License

MIT
