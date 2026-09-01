# LLM Observability: Langfuse tracing for AAX analyses

This document explains how MeshWeave exports LLM traces to [Langfuse](https://langfuse.com)
for the AAX (AI-powered site grading) pipeline. For Prometheus metrics and readiness
probes, see [observability.md](observability.md).

- What is traced: every LLM call made by `meshweave.ai` agents (pydantic-ai)
- Where it lives: `meshweave/ai/observability.py`
- Integration reference: <https://langfuse.com/integrations/frameworks/pydantic-ai>

---

## Overview

The AAX analysis runs a handful of LLM calls per site (homepage comprehension,
meta optimization, content delta, email validation, summary verdict). Each call is
an independent pydantic-ai agent run. With Langfuse enabled, those runs —
prompts, completions, token usage, model, latency — are exported as OpenTelemetry
spans to a Langfuse project, where they can be inspected, grouped per analysis,
and attributed to the user who triggered them.

The feature is **strictly opt-in**: without credentials in the environment, nothing
is initialized, no spans are exported, and there is no overhead beyond one env-var
check per process.

---

## How it works

Three pieces connect pydantic-ai to Langfuse:

1. **pydantic-ai instrumentation** — `Agent.instrument_all()` (called by
   `enable_langfuse()`) makes every agent run emit OpenTelemetry spans through the
   global tracer provider: one span for the agent run, one for each model request.
   Spans carry the request/response payloads and token counts.

2. **Langfuse client as the OTel exporter** — `get_client()` installs a
   `LangfuseSpanProcessor` on the same global tracer provider. pydantic-ai and
   Langfuse never call each other; they share the provider. The processor batches
   finished spans and ships them to the Langfuse API from background threads.

3. **Trace correlation** — each analysis run wraps its LLM calls in
   `trace_attributes(...)`, which stamps the OpenTelemetry context with
   `user.id`, `session.id`, and `langfuse.trace.tags`. Every span created inside
   the block inherits them, so one AAX analysis shows up as one session in the
   Langfuse UI instead of ~7 unrelated traces.

```
Agent.run() ──spans──▶ global TracerProvider ──▶ LangfuseSpanProcessor (batched)
                                                      │  background threads
                                                      ▼
                                        Langfuse API (region per LANGFUSE_BASE_URL)
```

---

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | yes (to enable) | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | yes (to enable) | Langfuse project secret key |
| `LANGFUSE_BASE_URL` | no | Region host. Default `https://cloud.langfuse.com` (EU). Others: `https://us.cloud.langfuse.com`, `https://jp.cloud.langfuse.com`, HIPAA `https://hipaa.cloud.langfuse.com` |

Tracing is enabled only when **both** keys are present. Get keys from the Langfuse
project settings (cloud or self-hosted).

- Local CLI runs: put the variables in `.env` (see `.env.example`); the CLI loads it.
- Docker deployments: both `docker-compose.yaml` and `docker-compose.prod.yaml`
  pass the three variables through to the webapp container; set them in `.env`
  / `.env.prod` respectively (see `.env.prod.example`).

After startup, credentials are verified once with a best-effort `auth_check()`.
A failure logs a warning ("Langfuse authentication failed" / "auth check raised")
but leaves tracing enabled — a transient network error at boot should not
silently disable tracing for a long-running process. Expect noisy export retries
in the logs if the keys are genuinely wrong.

---

## Where it's wired in

| Location | Role |
|---|---|
| `meshweave/ai/observability.py` | `enable_langfuse()` (init + gate), `trace_attributes()` (fail-soft propagation wrapper), `langfuse_client()`, `flush()` (atexit) |
| `meshweave/cli.py` (`main`) | Enables tracing for CLI runs; short-lived process relies on the atexit flush |
| `webapp/app.py` (lifespan) | Enables tracing when the webapp starts |
| `meshweave/ai/analyses.py` (`run_aax_analysis`) | Wraps each analysis in `trace_attributes(user_id, session_id, tags=["aax"])` |
| `webapp/services/scoring.py` (`run_aax_for_crawl`) | Passes the crawl owner as `user_id` and `aax:{crawl_id}` as `session_id` |

### Behavior details

- **Fail-soft everywhere.** Missing credentials, a missing `langfuse` package
  (logs a warning, not a traceback), init failures, and flush errors are all
  logged and swallowed. Observability can never take down a crawl or the webapp.
- **Idempotent enablement.** A module flag guards repeated `enable_langfuse()`
  calls, so only one atexit flush handler is ever registered.
- **Session ids.** The webapp uses `aax:{crawl_id}`, making each crawl's analysis
  a stable, re-findable session. Direct `run_aax_analysis` callers can pass their
  own `trace_session_id`; otherwise a random id is generated per run.

---

## What you see in Langfuse

- One **session** per AAX analysis (tagged `aax`), containing one trace per LLM
  call made by that analysis.
- `user.id` on those traces = the id of the user who triggered the crawl (webapp
  runs; absent for CLI runs).
- Standard Langfuse generation views: full prompt, model response, token usage,
  latency, and model name per call.

## Troubleshooting

- **No traces in the UI** — check the warning logs above first; then set
  `LANGFUSE_DEBUG=true` and rerun (see
  [Langfuse troubleshooting](https://langfuse.com/integrations/frameworks/pydantic-ai)).
  For short-lived CLI runs, the atexit flush handles export on exit.
- **"Langfuse authentication failed" warnings at startup** — keys or
  `LANGFUSE_BASE_URL` are wrong; spans are still exported but will be rejected.
- **CLI without `.env`** — the CLI loads `.env` via python-dotenv; exported shell
  variables work too.

## Privacy note

Instrumentation exports prompts and completions containing crawled third-party
page content and metadata. Email addresses in exported message attributes are
masked, and user email is not attached as trace metadata. Other site content
may still be sensitive, so point `LANGFUSE_BASE_URL` at a self-hosted instance
or review the data-processing implications before enabling tracing.

## Tests

`tests/test_observability.py` covers the gating logic (missing/partial
credentials), missing-package handling, idempotent enablement, auth-failure
behavior, flush, and `trace_attributes` no-op/propagation paths — using a fake
`langfuse` module, so no network access is needed.
