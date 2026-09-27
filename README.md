# MeshWeave

Find what prevents AI agents from reading your website correctly. MeshWeave diagnoses inaccessible content and empty or ambiguous copy, then prioritizes the fixes that make the site usable.

Every report answers three questions in the agent's journey order: can AI agents reach your site, answer from it, and act on it.

## Three Checks

| Check | Scores | Question |
| --- | --- | --- |
| **Reachable** | Site-wide machine context | Can agents reach your content and reconcile your business identity, evidence, and claims? |
| **Answerable** | Answer extractability | Does your content support direct, structured answers agents can extract? |
| **Actionable** | Agent actionability | Can agents identify your offer, understand the content, and find a credible next step? |

Scores are diagnostic measures of the website itself, not guarantees of outside outcomes. The actionability check is not an interactive browser-agent or transaction test.

## What You Get

- A free site review (core experience): enter a domain or URL
- Three check scores with factor breakdowns and rating bands
- **Prioritized recommendations** — every finding with expected point impact, ordered by priority
- **A grounded answerability test** — a fixed decision-critical benchmark over your crawled pages, with verdicts and source evidence
- **A client-ready report** — download as Markdown, share with clients or hand to an AI agent
- **A proof-of-work diff** — compare two runs to show resolved findings and observed per-check changes
- **An agency API** — bulk-submit up to 25 domains, fetch results as JSON, and download unbranded Markdown reports to hand to clients ([docs/api-contract.md](docs/api-contract.md))
- An [llms.txt file](webapp/static/.well-known/llms.txt) exposing this metadata to AI crawlers

## Quickstart

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Gemini API key (the Actionable check is LLM-powered). Get one at https://aistudio.google.com/
- Google OAuth credentials (for sign-in). Get them at https://console.cloud.google.com/apis/credentials

### 1. Clone and configure

```bash
git clone https://github.com/malvavisc0/meshweave.git
cd meshweave
cp .env.example .env
# Edit .env — set GEMINI_API_KEY (required), Google OAuth, and APP_SECRET_KEY (required for local runs)
```

Generate a session secret and paste it in `.env` as `APP_SECRET_KEY`:

```bash
uv run python -c 'import secrets; print(secrets.token_hex())'
```

### 2. Run (SQLite, zero external services)

```bash
make dev          # web UI at http://127.0.0.1:8057
make dev-worker   # API worker (separate terminal)
make dev-aax      # agent analysis worker (separate terminal)
```

### 3. Run (Postgres, Langfuse, production)

```bash
cp .env.production.example .env.production   # set DATABASE_URL to your Postgres URL
make dev-pg        # Postgres backend (requires Docker for the local Postgres container)
make worker        # API worker
make worker-aax    # agent analysis worker
make langfuse-up   # local Langfuse observability stack (optional)
```

## Documentation

| Document | Purpose |
| --- | --- |
| [docs/product.md](docs/product.md) | Product definition: problem, audience, positioning, checks, recommendation model |
| [docs/product-overview.md](docs/product-overview.md) | Product funnel, lead-capture logic, content pillars, and conversion path |
| [docs/funnel.md](docs/funnel.md) | Canonical acquisition funnel (30 questions across 5 stages) |
| [docs/funnels/30-questions.md](docs/funnels/30-questions.md) | The 30-question funnel detail |
| [docs/scoring-reference.md](docs/scoring-reference.md) | Scoring model: factors, weights, bands, compositional semantics |
| [docs/style-guide.md](docs/style-guide.md) | UI style guide: visual DNA, design tokens, HTML patterns |
| [docs/conversion-funnel.md](docs/conversion-funnel.md) | Conversion funnel and lead-capture design |
| [docs/observability.md](docs/observability.md) | LLM and application observability (Langfuse, SerpAPI, metrics) |
| [docs/market-research.md](docs/market-research.md) | Market research: buyer demand, ICP, competitive landscape, GTM plan |
| [docs/api-contract.md](docs/api-contract.md) | API contract: endpoints, auth, ownership, unbranded export, bulk limits |

## Architecture

- **`meshweave/`** — core Python library: Playwright-based crawling, screenshot capture, link mapping, page rendering, robots/llms.txt reading, 4 AI factor groups with expected-point remediation, and LLM analysis with normalized JSON schema
- **`webapp/`** — FastAPI web application with HTMX frontend, template-based UI, custom Litestar-style design system, Langfuse LLM observability, and Prometheus metrics (SQLite or PostgreSQL backend)
- **`worker/`** — API worker with durable DB-backed queue (resilient to crashes/restarts) and idempotent re-enqueue (a successful analysis refresh does not create a second queue entry)
- **`worker_aax/`** — agent analysis worker with durable DB-backed queue, idempotent scheduling, and LLM-powered analysis via LiteLLM
- **`tests/`** — 437 tests with multi-layer stubs for Playwright, Gemini, and FastAPI

See `make help` for all available commands.

## Environment

See [docs/observability.md](docs/observability.md) for Langfuse and metrics configuration.

## License

See [LICENSE](LICENSE).
