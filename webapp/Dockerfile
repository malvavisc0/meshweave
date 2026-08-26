# Webapp container for the public FastAPI UI that uses the meshweave library
# Installs Playwright (Chromium) and required OS dependencies.

# --- Build stage: includes Rust toolchain for compiling native extensions ---
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Install uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Rust toolchain for building native extensions (html-to-markdown)
# Use rustup for a recent rustc version (apt packages are too old)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

# Copy project packaging metadata and crawler library
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY meshweave ./meshweave

# Copy webapp source
COPY webapp ./webapp
# Copy Alembic migration config
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

# Install all dependencies via uv (locked + reproducible)
# - Sync core deps from uv.lock (meshweave crawler library)
# - Install the webapp package with its runtime extras
RUN uv sync --frozen --no-dev --no-install-project && \
    uv pip install --system . && \
    uv pip install --system /app/webapp[runtime]

# --- Final stage: minimal image without Rust ---
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MESHWEAVE_CACHE_DIR=/tmp/meshweave/cache \
    SQLITE_PATH=/app/app.db \
    PYTHONPATH=/app

# Install uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY webapp ./webapp
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

# Minify static assets in webapp before packaging
RUN uv pip install --system rjsmin rcssmin && \
    python - <<'PY'
from pathlib import Path
import sys
try:
    import rjsmin, rcssmin
except Exception as e:
    sys.exit(f"Minifier import failed: {e}")

root = Path("/app/webapp/static")
if not root.exists():
    sys.exit(0)

def is_binary(p: Path):
    try:
        with p.open("rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except Exception:
        return True

css_cnt = js_cnt = 0
for p in root.rglob("*.css"):
    if p.name.endswith(".min.css") or is_binary(p):
        continue
    try:
        s = p.read_text(encoding="utf-8")
        min_s = rcssmin.cssmin(s)
        if len(min_s) < len(s):
            p.write_text(min_s, encoding="utf-8")
        css_cnt += 1
    except Exception:
        pass

for p in root.rglob("*.js"):
    if p.name.endswith(".min.js") or is_binary(p):
        continue
    try:
        s = p.read_text(encoding="utf-8")
        min_s = rjsmin.jsmin(s)
        if len(min_s) < len(s):
            p.write_text(min_s, encoding="utf-8")
        js_cnt += 1
    except Exception:
        pass

print(f"Minified CSS:{css_cnt} JS:{js_cnt}")
PY
RUN uv pip uninstall --system rjsmin rcssmin || true

# Install curl for container healthchecks and diagnostics
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Pages are rendered by the LightPanda CDP service (docker-compose "lightpanda"),
# so no local Chromium/CloakBrowser binary is needed in this image.

# Create cache dir
RUN mkdir -p /tmp/meshweave/cache

EXPOSE 8080

# Run the FastAPI webapp UI
WORKDIR /app
CMD ["meshweave", "--host", "0.0.0.0", "--port", "8080"]
