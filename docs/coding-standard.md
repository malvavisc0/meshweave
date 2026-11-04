# MeshWeave Coding Standard

## Communication

You are talking to seniors. Be terse — no preamble, no recap, no
hand-holding, no "here's what I did" summaries. State results, not
intentions. Skip explanations unless explicitly asked. Never narrate
tool use. Answer the question; move on.

## Project

MeshWeave — a website audit for AI agents. A FastAPI web app plus a
reusable `meshweave` crawler library that audits how AI systems crawl,
understand, and cite websites. Reports score three public lenses —
**Reachable**, **Answerable**, **Actionable** — and return a prioritized
remediation roadmap. Public product copy names the product a "Website
audit for AI agents"; `AEO`/`GEO`/`AAX` stay internal identifiers for
score groups and code/API fields.

## Stack

- Python 3.14+, uv, `pyproject.toml` (setuptools backend)
- FastAPI + Pydantic + Jinja2 (Starlette `templates.TemplateResponse`)
- SQLAlchemy ORM (`webapp/models.py`), PostgreSQL 18, Alembic
  migrations; SQLite fallback for local and test runs
- Durable DB-backed job queues (no external broker), processed by
  in-process workers started from `webapp/app.py`
- `meshweave` CLI — `crawl` is the library entrypoint
- LLM scoring via an OpenAI-compatible endpoint, guarded by `LLM_*`
  and `AAX_ENABLED`
- Rendering via LightPanda CDP (`MESHWEAVE_CDP_ENDPOINT`); required,
  no local browser fallback
- Type checker: **mypy** (`[tool.mypy]` in `pyproject.toml` is active)
- Lint/format: **ruff** (line-length 88)
- Tests: **pytest** (`tests/`), with pytest-asyncio and pytest-xdist
- UI work follows `docs/style-guide.md` and
  `.kilo/skills/web-design-guidelines/SKILL.md`

## Commands

```bash
uv sync                              # install deps
uv run uvicorn webapp.main:app --host 0.0.0.0 --port 8080 --reload   # dev server
uv run ruff check --fix .            # lint
uv run ruff format .                 # format
uv run mypy                          # type check
uv run radon cc meshweave webapp -s  # complexity
uv run pytest -q -n auto             # tests
uv run alembic upgrade head          # DB migrations
docker compose up -d                 # full stack (postgres, lightpanda, webapp)
```

Run `ruff check`, `ruff format --check`, `mypy`, `radon cc`, and the
relevant tests after every change. CI enforces all five gates
(`.github/workflows/ci.yml`).

## Complexity budget

`radon cc` (cyclomatic complexity) must report **no block ranked C or
worse** — every function/method stays at rank A or B (complexity ≤ 10).
This applies to the whole codebase, not just new code: **a pre-existing
C/D/E/F block is still a defect and must always be fixed when
encountered** — never worked around, never left "for later". When a
change pushes a block to C+ (or you touch a file containing one), split
it into smaller functions (early returns, extract helpers, dispatch
tables) instead of adding `if` branches, and fix any pre-existing C+
blocks in the same pass. Verify with:

```bash
uv run radon cc meshweave webapp -s | grep -E '\-\s(C|D|E|F)\s'   # must be empty
```

## Dead code

Rules say "no dead code". Remove unused functions, fields, imports, and
tests in the same change that makes them unused. Do not keep unused code
alive with suppressions, no-op wrappers, or "for later" comments. There
is no automated dead-code gate yet, so reviewers and agents must check.

## Typing

Every new or modified function is fully annotated, including return
types. Pre-existing untyped functions are grandfathered until touched;
annotate one fully when changing it. The `mypy` gate does not yet run
with `--disallow-untyped-defs`, so this is review-enforced.

Baseline (2026-11-04): 179 untyped defs across 28 files.

## File size

No Python file over 600 lines in `meshweave/`, `webapp/`, or `tests/`.
A file already over the cap may still grow for docstrings or type
annotations; any other addition requires extracting code into a new
module first. When an over-cap file's logic is changed, split it by
responsibility (extract helpers, separate modules), not by truncating.
Check with:

```bash
find meshweave webapp tests -path '*/.venv' -prune -o -name '*.py' -print \
  | xargs wc -l | awk '$1 > 600 && $2 != "total"' | sort -rn
```

The output names the current offenders; a new entry, or growth beyond
docstrings/annotations, is not allowed.

Baseline (2026-11-04): 17 files exceed the cap (largest are
`meshweave/scoring/recommendations.py`, `webapp/routers/submissions.py`,
and `webapp/routers/api.py`). They are grandfathered and must be split by
responsibility when their logic is next changed.

## PII

Never use or commit PII — not in code, tests, docs, plans, fixtures,
examples, or commit messages. PII is any real person's or site's data:
names, email addresses, phone numbers, private site content, request
headers, or session ids tied to a person.

- Tests, docstrings, and examples use fictional identities only
  (`alice@example.com`-style addresses, invented names and domains).
- Error reports and incident notes describe the failure; they do not
  reproduce real user rows, emails, cookies, or site content.
- Real evidence (production logs, Sentry payloads, database exports)
  stays in local, untracked artifacts.
- Before committing, grep the diff for anything real you saw:
  `git diff | rg -i "<name|email|domain|cookie>"`.

## Priority

Clean, simple, maintainable code. Nothing else.

## Commits

One-line messages only. No body, no trailers.

## Rules

- DRY, KISS
- No unnecessary complexity
- No defensive fallbacks / over-engineering (resilient *fetching* with
  logging is intentional, not an exception to this)
- Explicit > implicit
- Type hints on all new and modified code (see Typing)
- PEP 8 (ruff enforces, line-length 88)
- Meaningful names, small functions
- Fail fast and clearly
- Prefer the standard library
- No clever tricks
- Docstrings for public APIs
- No unnecessary inline comments
- Use uv (never pip / system python)
- No mutable module-level globals
- No dead code
- No Python file over 600 lines in `meshweave/`, `webapp/`, or `tests/`
- Module-private helpers use a `_` prefix; this is the house style, not
  a defect to "fix"

## Style

Write the simplest correct solution. Delete anything that isn't needed.

## Conventions

- Web: routers stay thin (`webapp/routers/*.py`); data access lives in
  `webapp/models.py`; Jinja2 templates in `webapp/templates/`
  (`{% extends "base.html" %}`).
- Crawler library: `meshweave/crawling/` (fetcher, BFS engine,
  sitemap), `meshweave/extraction/` (markdown, links, emails, meta),
  `meshweave/scoring/` (AEO/GEO/AAX engines), `meshweave/ai/` (LLM
  runner).
- Cache is disk-based (`MESHWEAVE_CACHE_DIR`); rendered HTML is cached
  by URL + render params.
- LLM scoring is optional — guard on `LLM_*` / `AAX_ENABLED` env vars.
- Rendering routes through the LightPanda CDP service; it is required,
  so set `MESHWEAVE_CDP_ENDPOINT` (e.g. `http://lightpanda:9222`).
- Public surfaces use Reachable / Answerable / Actionable. AEO, GEO,
  and AAX remain internal-only (score groups, code identifiers, API
  fields); do not leak the acronyms into customer-facing copy.
- Public copy is shipped copy: it makes no outside-outcome promises
  (rankings, citations, traffic, conversions). `tests/test_public_copy_sweep.py`
  enforces the current banned-claim list.
- Durable queue jobs are crash-safe and idempotent: a retry or restart
  must not create a second job or double-count a crawl.
- The initial schema is
  `alembic/versions/880764c4c4b4_initial_schema.py`. Never edit an
  already-applied migration; add a new revision chained to it.
  `tests/test_launch_deployment.py` asserts a linear migration chain and
  the initial migration's contents.
- Environment configuration is read at the boundary (`webapp/utils/config.py`
  and env vars); production must fail closed on missing secrets rather
  than falling back to development defaults.
