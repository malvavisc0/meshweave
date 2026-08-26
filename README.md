# MeshWeave

> AI visibility risk analysis for citation, discovery, and agent trust.

MeshWeave audits how AI systems crawl, understand, and cite websites. It identifies structural weaknesses that limit visibility, citation confidence, generative discovery, and AI agent trust — then delivers a prioritized remediation roadmap.

## Three Risk Lenses

| Lens | What It Measures | Business Risk |
|------|-----------------|---------------|
| **AEO** — Answer Engine Optimization | Can AI extract clear, trustworthy answers from your content? | Your brand is not quoted. Competitors own the answer. |
| **GEO** — Generative Engine Optimization | Does AI see sufficient authority, trust, and entity signals? | Your brand is overlooked in recommendations. Demand leaks. |
| **AAX** — AI Agent Experience | Can AI agents understand, navigate, and act on your site? | Agent-mediated buyers abandon before conversion. |

## Quick Start

### Docker Compose (recommended)

```bash
cp .env.example .env   # configure OAuth, LLM, base URL
docker compose up -d
```

The webapp is available at `http://localhost:8080`.

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

## CLI

The CLI provides the `crawl` subcommand. Running `meshweave <url>` without a subcommand defaults to `crawl`.

```bash
# Single page
meshweave crawl https://example.com

# Site crawl (internal links, up to 50 pages)
meshweave crawl https://example.com \
  --crawl-internal --max-pages 50 \
  -o output.json

# Markdown output per page
meshweave crawl https://example.com \
  --crawl-internal --max-pages 10 \
  --output-dir ./data/output
```

Run `meshweave crawl --help` for all options.

## Library Usage

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
    )
    # payload contains: page, markdown, links, metrics,
    # emails, crawl info, audit, headings, and more


asyncio.run(main())
```

## Web App

A full-featured FastAPI web application for submitting URLs and viewing AI visibility analysis results.

### Key capabilities

- **Site & page analysis** — Submit URLs for crawling with configurable depth and page limits
- **AEO / GEO / AAX scoring** — LLM-powered scoring across three risk lenses with detailed breakdowns
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
| `FOOTER_CONTACT_EMAIL` | Contact email in footer/legal | `hello@meshweaveai.com` |

See `docker-compose.yaml` for the full set of configuration options.

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

## License

MIT
